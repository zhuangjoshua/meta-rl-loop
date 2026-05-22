CREATE TABLE IF NOT EXISTS generated_app_builds (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  workflow_job_id uuid REFERENCES workflow_jobs(id) ON DELETE SET NULL,
  status text NOT NULL DEFAULT 'queued',
  source_dir text NOT NULL,
  manifest jsonb NOT NULL DEFAULT '{}'::jsonb,
  install_log text,
  typecheck_log text,
  build_log text,
  smoke_log text,
  error text,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE generated_app_builds DROP CONSTRAINT IF EXISTS generated_app_builds_status_check;
ALTER TABLE generated_app_builds
  ADD CONSTRAINT generated_app_builds_status_check
  CHECK (status IN ('queued', 'running', 'completed', 'blocked', 'failed', 'cancelled'));

CREATE TABLE IF NOT EXISTS generated_app_build_steps (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  build_id uuid NOT NULL REFERENCES generated_app_builds(id) ON DELETE CASCADE,
  step_key text NOT NULL,
  status text NOT NULL DEFAULT 'queued',
  log text,
  error text,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (build_id, step_key)
);

CREATE TABLE IF NOT EXISTS generated_app_deployments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  build_id uuid NOT NULL REFERENCES generated_app_builds(id) ON DELETE CASCADE,
  status text NOT NULL DEFAULT 'queued',
  deployment_url text,
  alias_url text,
  health_status text,
  health_checked_at timestamptz,
  receipt jsonb NOT NULL DEFAULT '{}'::jsonb,
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE generated_app_deployments DROP CONSTRAINT IF EXISTS generated_app_deployments_status_check;
ALTER TABLE generated_app_deployments
  ADD CONSTRAINT generated_app_deployments_status_check
  CHECK (status IN ('queued', 'running', 'completed', 'blocked', 'failed', 'cancelled'));

CREATE TABLE IF NOT EXISTS generated_app_runtime_manifests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  generated_app_run_id text NOT NULL,
  runtime text NOT NULL,
  npm_packages text[] NOT NULL DEFAULT ARRAY[]::text[],
  required_capabilities text[] NOT NULL DEFAULT ARRAY[]::text[],
  setup_required_capabilities text[] NOT NULL DEFAULT ARRAY[]::text[],
  notes text NOT NULL DEFAULT '',
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS generated_app_users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  email text NOT NULL,
  name text,
  status text NOT NULL DEFAULT 'active',
  tier text NOT NULL DEFAULT 'free',
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (business_id, email)
);

ALTER TABLE generated_app_users
  ADD COLUMN IF NOT EXISTS name text,
  ADD COLUMN IF NOT EXISTS tier text NOT NULL DEFAULT 'free',
  ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS generated_app_magic_links (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  generated_app_user_id uuid REFERENCES generated_app_users(id) ON DELETE CASCADE,
  token_hash text NOT NULL UNIQUE,
  purpose text NOT NULL DEFAULT 'login',
  expires_at timestamptz NOT NULL,
  consumed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS generated_app_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  app_user_id uuid NOT NULL REFERENCES generated_app_users(id) ON DELETE CASCADE,
  token_hash text NOT NULL UNIQUE,
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS generated_app_entitlements (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  app_user_id uuid NOT NULL REFERENCES generated_app_users(id) ON DELETE CASCADE,
  tier text NOT NULL DEFAULT 'free',
  status text NOT NULL DEFAULT 'active',
  source text NOT NULL DEFAULT 'free',
  stripe_customer_id text,
  stripe_subscription_id text,
  company_payment_link_id uuid,
  current_period_end timestamptz,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE generated_app_entitlements
  ADD COLUMN IF NOT EXISTS app_user_id uuid REFERENCES generated_app_users(id) ON DELETE CASCADE,
  ADD COLUMN IF NOT EXISTS tier text NOT NULL DEFAULT 'free',
  ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'free',
  ADD COLUMN IF NOT EXISTS stripe_customer_id text,
  ADD COLUMN IF NOT EXISTS stripe_subscription_id text,
  ADD COLUMN IF NOT EXISTS company_payment_link_id uuid,
  ADD COLUMN IF NOT EXISTS current_period_end timestamptz,
  ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS generated_app_plan_policies (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  company_payment_link_id uuid,
  plan_key text NOT NULL,
  tier text NOT NULL DEFAULT 'free',
  price_usd_cents integer NOT NULL DEFAULT 0,
  billing_interval text NOT NULL DEFAULT 'month',
  included_ai_budget_microusd bigint NOT NULL DEFAULT 0,
  included_action_quota integer NOT NULL DEFAULT 25,
  estimated_cac_microusd bigint,
  estimated_non_ai_cogs_microusd bigint,
  target_margin_bps integer,
  creator_revenue_share_bps integer,
  allow_overage boolean NOT NULL DEFAULT false,
  overage_source text,
  source text NOT NULL DEFAULT 'argon_default',
  notes text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (business_id, plan_key)
);

CREATE TABLE IF NOT EXISTS project_ai_wallets (
  business_id uuid PRIMARY KEY REFERENCES businesses(id) ON DELETE CASCADE,
  status text NOT NULL DEFAULT 'active',
  hard_limit_microusd bigint NOT NULL DEFAULT 5000000,
  current_period_start timestamptz NOT NULL DEFAULT date_trunc('month', now()),
  current_period_end timestamptz NOT NULL DEFAULT date_trunc('month', now()) + interval '1 month',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS project_ai_proxy_keys (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  name text NOT NULL DEFAULT 'Generated app runtime',
  key_hash text NOT NULL UNIQUE,
  key_prefix text NOT NULL,
  last_four text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'active',
  last_used_at timestamptz,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS project_ai_model_policies (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  purpose text NOT NULL,
  provider text NOT NULL,
  model text NOT NULL,
  quality_tier text NOT NULL DEFAULT 'fast',
  allowed boolean NOT NULL DEFAULT true,
  max_input_tokens integer NOT NULL DEFAULT 4000,
  max_output_tokens integer NOT NULL DEFAULT 800,
  max_estimated_cost_microusd integer NOT NULL DEFAULT 500000,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (business_id, purpose)
);

CREATE TABLE IF NOT EXISTS project_ai_usage_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  proxy_key_id uuid REFERENCES project_ai_proxy_keys(id) ON DELETE SET NULL,
  app_user_key text,
  app_user_tier text,
  purpose text NOT NULL,
  route text NOT NULL,
  status text NOT NULL,
  estimated_cost_microusd bigint NOT NULL DEFAULT 0,
  actual_cost_microusd bigint NOT NULL DEFAULT 0,
  input_tokens integer,
  output_tokens integer,
  provider_request_id text,
  provider text,
  model text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz
);

CREATE TABLE IF NOT EXISTS generated_app_product_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  generated_app_user_id uuid REFERENCES generated_app_users(id) ON DELETE SET NULL,
  status text NOT NULL DEFAULT 'completed',
  input jsonb NOT NULL DEFAULT '{}'::jsonb,
  output jsonb NOT NULL DEFAULT '{}'::jsonb,
  error text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS generated_app_builds_business_created_idx ON generated_app_builds(business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS generated_app_deployments_business_created_idx ON generated_app_deployments(business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS generated_app_users_business_idx ON generated_app_users(business_id, email);
CREATE INDEX IF NOT EXISTS generated_app_entitlements_user_idx ON generated_app_entitlements(app_user_id, status);
CREATE INDEX IF NOT EXISTS project_ai_usage_business_created_idx ON project_ai_usage_events(business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS generated_app_product_runs_business_created_idx ON generated_app_product_runs(business_id, created_at DESC);

DROP TRIGGER IF EXISTS generated_app_builds_set_updated_at ON generated_app_builds;
CREATE TRIGGER generated_app_builds_set_updated_at BEFORE UPDATE ON generated_app_builds FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS generated_app_deployments_set_updated_at ON generated_app_deployments;
CREATE TRIGGER generated_app_deployments_set_updated_at BEFORE UPDATE ON generated_app_deployments FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS generated_app_runtime_manifests_set_updated_at ON generated_app_runtime_manifests;
CREATE TRIGGER generated_app_runtime_manifests_set_updated_at BEFORE UPDATE ON generated_app_runtime_manifests FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS generated_app_users_set_updated_at ON generated_app_users;
CREATE TRIGGER generated_app_users_set_updated_at BEFORE UPDATE ON generated_app_users FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS generated_app_entitlements_set_updated_at ON generated_app_entitlements;
CREATE TRIGGER generated_app_entitlements_set_updated_at BEFORE UPDATE ON generated_app_entitlements FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS generated_app_plan_policies_set_updated_at ON generated_app_plan_policies;
CREATE TRIGGER generated_app_plan_policies_set_updated_at BEFORE UPDATE ON generated_app_plan_policies FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS project_ai_wallets_set_updated_at ON project_ai_wallets;
CREATE TRIGGER project_ai_wallets_set_updated_at BEFORE UPDATE ON project_ai_wallets FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS project_ai_model_policies_set_updated_at ON project_ai_model_policies;
CREATE TRIGGER project_ai_model_policies_set_updated_at BEFORE UPDATE ON project_ai_model_policies FOR EACH ROW EXECUTE FUNCTION set_updated_at();
