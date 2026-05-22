CREATE TABLE IF NOT EXISTS provider_integrations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider text NOT NULL,
  scope_type text NOT NULL DEFAULT 'platform',
  business_id uuid REFERENCES businesses(id) ON DELETE CASCADE,
  profile_id uuid REFERENCES profiles(id) ON DELETE CASCADE,
  generated_app_user_id uuid REFERENCES generated_app_users(id) ON DELETE CASCADE,
  status text NOT NULL DEFAULT 'not_configured',
  public_config jsonb NOT NULL DEFAULT '{}'::jsonb,
  encrypted_config jsonb NOT NULL DEFAULT '{}'::jsonb,
  key_version text NOT NULL DEFAULT 'v1',
  last_verified_at timestamptz,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE provider_integrations DROP CONSTRAINT IF EXISTS provider_integrations_scope_check;
ALTER TABLE provider_integrations
  ADD CONSTRAINT provider_integrations_scope_check
  CHECK (scope_type IN ('platform', 'business', 'profile', 'generated_app'));

ALTER TABLE provider_integrations DROP CONSTRAINT IF EXISTS provider_integrations_status_check;
ALTER TABLE provider_integrations
  ADD CONSTRAINT provider_integrations_status_check
  CHECK (status IN ('not_configured', 'active', 'paused', 'error', 'revoked'));

CREATE UNIQUE INDEX IF NOT EXISTS provider_integrations_scope_uidx
  ON provider_integrations (
    provider,
    scope_type,
    COALESCE(business_id, '00000000-0000-0000-0000-000000000000'::uuid),
    COALESCE(profile_id, '00000000-0000-0000-0000-000000000000'::uuid),
    COALESCE(generated_app_user_id, '00000000-0000-0000-0000-000000000000'::uuid)
  );

CREATE TABLE IF NOT EXISTS business_campaigns (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  slug text NOT NULL,
  name text NOT NULL,
  kind text NOT NULL DEFAULT 'campaign',
  status text NOT NULL DEFAULT 'draft',
  workspace_path text NOT NULL,
  budget_cap_microusd bigint,
  started_at timestamptz,
  ended_at timestamptz,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_by_profile_id uuid REFERENCES profiles(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (business_id, slug)
);

ALTER TABLE business_campaigns DROP CONSTRAINT IF EXISTS business_campaigns_status_check;
ALTER TABLE business_campaigns
  ADD CONSTRAINT business_campaigns_status_check
  CHECK (status IN ('draft', 'active', 'paused', 'completed', 'failed', 'killed', 'archived'));

CREATE TABLE IF NOT EXISTS business_memory_records (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  campaign_id uuid REFERENCES business_campaigns(id) ON DELETE SET NULL,
  namespace text NOT NULL DEFAULT 'strategy',
  memory_key text NOT NULL,
  title text NOT NULL DEFAULT '',
  content text NOT NULL DEFAULT '',
  evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'active',
  confidence numeric,
  created_by_profile_id uuid REFERENCES profiles(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (business_id, namespace, memory_key)
);

ALTER TABLE business_memory_records DROP CONSTRAINT IF EXISTS business_memory_records_status_check;
ALTER TABLE business_memory_records
  ADD CONSTRAINT business_memory_records_status_check
  CHECK (status IN ('active', 'superseded', 'archived'));

CREATE TABLE IF NOT EXISTS business_budget_accounts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  campaign_id uuid REFERENCES business_campaigns(id) ON DELETE CASCADE,
  name text NOT NULL DEFAULT 'default',
  currency text NOT NULL DEFAULT 'USD',
  status text NOT NULL DEFAULT 'active',
  hard_limit_microusd bigint NOT NULL DEFAULT 0,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE business_budget_accounts DROP CONSTRAINT IF EXISTS business_budget_accounts_status_check;
ALTER TABLE business_budget_accounts
  ADD CONSTRAINT business_budget_accounts_status_check
  CHECK (status IN ('active', 'frozen', 'killed', 'archived'));

CREATE TABLE IF NOT EXISTS business_budget_ledger (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  budget_account_id uuid REFERENCES business_budget_accounts(id) ON DELETE SET NULL,
  campaign_id uuid REFERENCES business_campaigns(id) ON DELETE SET NULL,
  workflow_job_id uuid REFERENCES workflow_jobs(id) ON DELETE SET NULL,
  profile_id uuid REFERENCES profiles(id) ON DELETE SET NULL,
  kind text NOT NULL,
  status text NOT NULL DEFAULT 'active',
  amount_microusd bigint NOT NULL,
  currency text NOT NULL DEFAULT 'USD',
  provider text,
  external_ref text,
  purpose text NOT NULL DEFAULT '',
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE business_budget_ledger DROP CONSTRAINT IF EXISTS business_budget_ledger_kind_check;
ALTER TABLE business_budget_ledger
  ADD CONSTRAINT business_budget_ledger_kind_check
  CHECK (kind IN ('allocation', 'reservation', 'commit', 'release', 'refund', 'adjustment'));

ALTER TABLE business_budget_ledger DROP CONSTRAINT IF EXISTS business_budget_ledger_status_check;
ALTER TABLE business_budget_ledger
  ADD CONSTRAINT business_budget_ledger_status_check
  CHECK (status IN ('active', 'committed', 'released', 'cancelled', 'failed'));

CREATE TABLE IF NOT EXISTS takyon_control_states (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  scope_type text NOT NULL,
  business_id uuid REFERENCES businesses(id) ON DELETE CASCADE,
  campaign_id uuid REFERENCES business_campaigns(id) ON DELETE CASCADE,
  workflow_job_id uuid REFERENCES workflow_jobs(id) ON DELETE CASCADE,
  agent_run_id uuid REFERENCES agent_runs(id) ON DELETE CASCADE,
  provider text,
  scope_key text NOT NULL,
  state text NOT NULL DEFAULT 'active',
  reason text NOT NULL DEFAULT '',
  actor_profile_id uuid REFERENCES profiles(id) ON DELETE SET NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (scope_type, scope_key)
);

ALTER TABLE takyon_control_states DROP CONSTRAINT IF EXISTS takyon_control_states_scope_check;
ALTER TABLE takyon_control_states
  ADD CONSTRAINT takyon_control_states_scope_check
  CHECK (scope_type IN ('global', 'business', 'campaign', 'workflow_job', 'agent_run', 'provider'));

ALTER TABLE takyon_control_states DROP CONSTRAINT IF EXISTS takyon_control_states_state_check;
ALTER TABLE takyon_control_states
  ADD CONSTRAINT takyon_control_states_state_check
  CHECK (state IN ('active', 'paused', 'killed'));

CREATE INDEX IF NOT EXISTS provider_integrations_business_idx ON provider_integrations(provider, business_id, profile_id);
CREATE INDEX IF NOT EXISTS business_campaigns_business_status_idx ON business_campaigns(business_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS business_memory_records_business_idx ON business_memory_records(business_id, namespace, updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS business_budget_accounts_scope_uidx
  ON business_budget_accounts (
    business_id,
    COALESCE(campaign_id, '00000000-0000-0000-0000-000000000000'::uuid),
    name
  );
CREATE INDEX IF NOT EXISTS business_budget_ledger_business_idx ON business_budget_ledger(business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS business_budget_ledger_campaign_idx ON business_budget_ledger(campaign_id, created_at DESC);
CREATE INDEX IF NOT EXISTS takyon_control_states_business_idx ON takyon_control_states(business_id, state);
CREATE INDEX IF NOT EXISTS takyon_control_states_campaign_idx ON takyon_control_states(campaign_id, state);

DROP TRIGGER IF EXISTS provider_integrations_set_updated_at ON provider_integrations;
CREATE TRIGGER provider_integrations_set_updated_at BEFORE UPDATE ON provider_integrations FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS business_campaigns_set_updated_at ON business_campaigns;
CREATE TRIGGER business_campaigns_set_updated_at BEFORE UPDATE ON business_campaigns FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS business_memory_records_set_updated_at ON business_memory_records;
CREATE TRIGGER business_memory_records_set_updated_at BEFORE UPDATE ON business_memory_records FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS business_budget_accounts_set_updated_at ON business_budget_accounts;
CREATE TRIGGER business_budget_accounts_set_updated_at BEFORE UPDATE ON business_budget_accounts FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS business_budget_ledger_set_updated_at ON business_budget_ledger;
CREATE TRIGGER business_budget_ledger_set_updated_at BEFORE UPDATE ON business_budget_ledger FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS takyon_control_states_set_updated_at ON takyon_control_states;
CREATE TRIGGER takyon_control_states_set_updated_at BEFORE UPDATE ON takyon_control_states FOR EACH ROW EXECUTE FUNCTION set_updated_at();

INSERT INTO provider_integrations (
  provider,
  scope_type,
  status,
  public_config,
  encrypted_config,
  key_version,
  last_verified_at,
  last_error,
  created_at,
  updated_at
)
SELECT
  'x',
  'platform',
  status,
  public_config,
  encrypted_config,
  key_version,
  last_verified_at,
  last_error,
  created_at,
  updated_at
FROM platform_integrations
WHERE id = 'x_platform'
ON CONFLICT (
  provider,
  scope_type,
  COALESCE(business_id, '00000000-0000-0000-0000-000000000000'::uuid),
  COALESCE(profile_id, '00000000-0000-0000-0000-000000000000'::uuid),
  COALESCE(generated_app_user_id, '00000000-0000-0000-0000-000000000000'::uuid)
)
DO NOTHING;
