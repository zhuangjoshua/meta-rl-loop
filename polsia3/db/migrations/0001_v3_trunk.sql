CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS _migrations (
  name text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

CREATE TABLE IF NOT EXISTS profiles (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  clerk_user_id text UNIQUE,
  auth_provider text,
  auth_subject text,
  email text NOT NULL,
  name text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE profiles
  ADD COLUMN IF NOT EXISTS auth_provider text,
  ADD COLUMN IF NOT EXISTS auth_subject text;

UPDATE profiles
SET auth_provider = COALESCE(auth_provider, 'legacy'),
    auth_subject = COALESCE(auth_subject, clerk_user_id, id::text)
WHERE auth_provider IS NULL OR auth_subject IS NULL;

ALTER TABLE profiles
  ALTER COLUMN auth_provider SET NOT NULL,
  ALTER COLUMN auth_subject SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS profiles_auth_provider_subject_uidx
  ON profiles(auth_provider, auth_subject);

CREATE TABLE IF NOT EXISTS businesses (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_profile_id uuid NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
  name text NOT NULL,
  slug text NOT NULL UNIQUE,
  status text NOT NULL DEFAULT 'active',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE businesses DROP CONSTRAINT IF EXISTS businesses_status_check;
ALTER TABLE businesses
  ADD CONSTRAINT businesses_status_check
  CHECK (status IN ('active', 'paused', 'archived'));

CREATE TABLE IF NOT EXISTS business_memberships (
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  profile_id uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  role text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (business_id, profile_id)
);

ALTER TABLE business_memberships DROP CONSTRAINT IF EXISTS business_memberships_role_check;
ALTER TABLE business_memberships
  ADD CONSTRAINT business_memberships_role_check
  CHECK (role IN ('owner', 'admin', 'operator', 'viewer'));

CREATE TABLE IF NOT EXISTS company_sites (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL UNIQUE REFERENCES businesses(id) ON DELETE CASCADE,
  slug text NOT NULL UNIQUE,
  status text NOT NULL DEFAULT 'draft',
  base_domain text,
  public_title text NOT NULL,
  public_pitch text NOT NULL DEFAULT '',
  config jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE company_sites DROP CONSTRAINT IF EXISTS company_sites_status_check;
ALTER TABLE company_sites
  ADD CONSTRAINT company_sites_status_check
  CHECK (status IN ('draft', 'building', 'published', 'offline', 'failed', 'blocked'));

CREATE TABLE IF NOT EXISTS tasks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  title text NOT NULL,
  description text NOT NULL DEFAULT '',
  category text NOT NULL DEFAULT 'general',
  status text NOT NULL DEFAULT 'queued',
  priority integer NOT NULL DEFAULT 0,
  created_by_profile_id uuid REFERENCES profiles(id) ON DELETE SET NULL,
  claimed_by text,
  due_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_status_check;
ALTER TABLE tasks
  ADD CONSTRAINT tasks_status_check
  CHECK (status IN ('queued', 'running', 'completed', 'blocked', 'failed', 'cancelled'));

CREATE TABLE IF NOT EXISTS events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid REFERENCES businesses(id) ON DELETE CASCADE,
  actor_profile_id uuid REFERENCES profiles(id) ON DELETE SET NULL,
  kind text NOT NULL,
  subject_type text,
  subject_id uuid,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS business_documents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  title text NOT NULL,
  kind text NOT NULL DEFAULT 'document',
  content text NOT NULL DEFAULT '',
  source text NOT NULL DEFAULT 'agent',
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (business_id, title)
);

ALTER TABLE business_documents DROP CONSTRAINT IF EXISTS business_documents_kind_check;
ALTER TABLE business_documents
  ADD CONSTRAINT business_documents_kind_check
  CHECK (kind IN ('mission', 'research_report', 'daily_report', 'task_report', 'website_brief', 'document'));

ALTER TABLE business_documents DROP CONSTRAINT IF EXISTS business_documents_source_check;
ALTER TABLE business_documents
  ADD CONSTRAINT business_documents_source_check
  CHECK (source IN ('agent', 'workflow', 'system', 'operator'));

CREATE TABLE IF NOT EXISTS business_inbox_messages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  author_profile_id uuid REFERENCES profiles(id) ON DELETE SET NULL,
  author_label text NOT NULL,
  body text NOT NULL,
  source text NOT NULL DEFAULT 'dashboard',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workflow_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  profile_id uuid REFERENCES profiles(id) ON DELETE SET NULL,
  task_id uuid REFERENCES tasks(id) ON DELETE SET NULL,
  workflow_id text NOT NULL,
  lane text NOT NULL DEFAULT 'foundation',
  status text NOT NULL DEFAULT 'queued',
  priority integer NOT NULL DEFAULT 50,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  dependencies text[] NOT NULL DEFAULT ARRAY[]::text[],
  result jsonb NOT NULL DEFAULT '{}'::jsonb,
  error text,
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  max_attempts integer NOT NULL DEFAULT 2 CHECK (max_attempts > 0),
  run_after timestamptz NOT NULL DEFAULT now(),
  locked_by text,
  locked_at timestamptz,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE workflow_jobs
  ADD COLUMN IF NOT EXISTS lane text NOT NULL DEFAULT 'foundation',
  ADD COLUMN IF NOT EXISTS dependencies text[] NOT NULL DEFAULT ARRAY[]::text[];

UPDATE workflow_jobs
SET status = 'failed'
WHERE status = 'degraded';

ALTER TABLE workflow_jobs DROP CONSTRAINT IF EXISTS workflow_jobs_status_check;
ALTER TABLE workflow_jobs
  ADD CONSTRAINT workflow_jobs_status_check
  CHECK (status IN ('queued', 'running', 'completed', 'blocked', 'failed', 'cancelled'));

ALTER TABLE workflow_jobs DROP CONSTRAINT IF EXISTS workflow_jobs_lane_check;
ALTER TABLE workflow_jobs
  ADD CONSTRAINT workflow_jobs_lane_check
  CHECK (lane IN (
    'foundation',
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
    'ceo'
  ));

CREATE TABLE IF NOT EXISTS prompts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  prompt_key text NOT NULL UNIQUE,
  title text NOT NULL,
  functionality text NOT NULL DEFAULT '',
  active_version_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS prompt_versions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  prompt_id uuid NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
  version integer NOT NULL,
  content text NOT NULL,
  change_note text NOT NULL DEFAULT '',
  edited_by_profile_id uuid REFERENCES profiles(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (prompt_id, version)
);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'prompts_active_version_id_fkey'
  ) THEN
    ALTER TABLE prompts
      ADD CONSTRAINT prompts_active_version_id_fkey
      FOREIGN KEY (active_version_id) REFERENCES prompt_versions(id) ON DELETE SET NULL;
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS agent_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  task_id uuid REFERENCES tasks(id) ON DELETE SET NULL,
  workflow_job_id uuid REFERENCES workflow_jobs(id) ON DELETE SET NULL,
  workflow_id text NOT NULL,
  addon_key text,
  agent_key text NOT NULL,
  status text NOT NULL DEFAULT 'queued',
  input_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  prompt_id uuid REFERENCES prompts(id) ON DELETE SET NULL,
  prompt_version_id uuid REFERENCES prompt_versions(id) ON DELETE SET NULL,
  output jsonb NOT NULL DEFAULT '{}'::jsonb,
  error text,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE agent_runs DROP CONSTRAINT IF EXISTS agent_runs_status_check;
ALTER TABLE agent_runs
  ADD CONSTRAINT agent_runs_status_check
  CHECK (status IN ('queued', 'running', 'completed', 'blocked', 'failed', 'cancelled'));

CREATE TABLE IF NOT EXISTS agent_run_steps (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id uuid NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
  step_index integer NOT NULL,
  tool_name text NOT NULL,
  input jsonb NOT NULL DEFAULT '{}'::jsonb,
  output jsonb NOT NULL DEFAULT '{}'::jsonb,
  error text,
  receipt_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (run_id, step_index)
);

CREATE TABLE IF NOT EXISTS cron_jobs (
  job_key text PRIMARY KEY,
  status text NOT NULL DEFAULT 'active',
  schedule_type text NOT NULL,
  interval_seconds integer,
  daily_time_utc time,
  default_limit integer NOT NULL DEFAULT 3,
  next_run_at timestamptz NOT NULL DEFAULT now(),
  last_started_at timestamptz,
  last_completed_at timestamptz,
  last_result jsonb NOT NULL DEFAULT '{}'::jsonb,
  last_error text,
  locked_by text,
  locked_at timestamptz,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE cron_jobs DROP CONSTRAINT IF EXISTS cron_jobs_status_check;
ALTER TABLE cron_jobs
  ADD CONSTRAINT cron_jobs_status_check
  CHECK (status IN ('active', 'paused'));

ALTER TABLE cron_jobs DROP CONSTRAINT IF EXISTS cron_jobs_schedule_check;
ALTER TABLE cron_jobs
  ADD CONSTRAINT cron_jobs_schedule_check
  CHECK (
    (schedule_type = 'interval' AND interval_seconds IS NOT NULL AND interval_seconds >= 60 AND daily_time_utc IS NULL)
    OR
    (schedule_type = 'daily' AND daily_time_utc IS NOT NULL)
  );

CREATE TABLE IF NOT EXISTS addons (
  addon_key text PRIMARY KEY,
  title text NOT NULL,
  status text NOT NULL DEFAULT 'available',
  config_schema jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS company_addons (
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  addon_key text NOT NULL REFERENCES addons(addon_key) ON DELETE CASCADE,
  status text NOT NULL DEFAULT 'enabled',
  config jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (business_id, addon_key)
);

CREATE TABLE IF NOT EXISTS action_policies (
  policy_key text PRIMARY KEY,
  title text NOT NULL,
  default_requires_approval boolean NOT NULL DEFAULT true,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS company_action_policies (
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  policy_key text NOT NULL REFERENCES action_policies(policy_key) ON DELETE CASCADE,
  requires_approval boolean NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (business_id, policy_key)
);

CREATE INDEX IF NOT EXISTS businesses_owner_profile_id_idx ON businesses(owner_profile_id);
CREATE INDEX IF NOT EXISTS business_memberships_profile_id_idx ON business_memberships(profile_id);
CREATE INDEX IF NOT EXISTS company_sites_business_id_idx ON company_sites(business_id);
CREATE INDEX IF NOT EXISTS tasks_business_id_status_idx ON tasks(business_id, status);
CREATE INDEX IF NOT EXISTS events_business_id_created_at_idx ON events(business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS business_documents_business_created_idx ON business_documents(business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS business_inbox_messages_business_created_idx ON business_inbox_messages(business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS workflow_jobs_claim_idx ON workflow_jobs(status, run_after, priority DESC, created_at ASC);
CREATE INDEX IF NOT EXISTS workflow_jobs_business_created_idx ON workflow_jobs(business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS workflow_jobs_task_idx ON workflow_jobs(task_id) WHERE task_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS agent_runs_business_created_idx ON agent_runs(business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS agent_run_steps_run_idx ON agent_run_steps(run_id, step_index);
CREATE INDEX IF NOT EXISTS cron_jobs_due_idx ON cron_jobs(status, next_run_at);

DROP TRIGGER IF EXISTS profiles_set_updated_at ON profiles;
CREATE TRIGGER profiles_set_updated_at BEFORE UPDATE ON profiles FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS businesses_set_updated_at ON businesses;
CREATE TRIGGER businesses_set_updated_at BEFORE UPDATE ON businesses FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS company_sites_set_updated_at ON company_sites;
CREATE TRIGGER company_sites_set_updated_at BEFORE UPDATE ON company_sites FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS tasks_set_updated_at ON tasks;
CREATE TRIGGER tasks_set_updated_at BEFORE UPDATE ON tasks FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS business_documents_set_updated_at ON business_documents;
CREATE TRIGGER business_documents_set_updated_at BEFORE UPDATE ON business_documents FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS workflow_jobs_set_updated_at ON workflow_jobs;
CREATE TRIGGER workflow_jobs_set_updated_at BEFORE UPDATE ON workflow_jobs FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS prompts_set_updated_at ON prompts;
CREATE TRIGGER prompts_set_updated_at BEFORE UPDATE ON prompts FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS agent_runs_set_updated_at ON agent_runs;
CREATE TRIGGER agent_runs_set_updated_at BEFORE UPDATE ON agent_runs FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS cron_jobs_set_updated_at ON cron_jobs;
CREATE TRIGGER cron_jobs_set_updated_at BEFORE UPDATE ON cron_jobs FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS addons_set_updated_at ON addons;
CREATE TRIGGER addons_set_updated_at BEFORE UPDATE ON addons FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS company_addons_set_updated_at ON company_addons;
CREATE TRIGGER company_addons_set_updated_at BEFORE UPDATE ON company_addons FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS action_policies_set_updated_at ON action_policies;
CREATE TRIGGER action_policies_set_updated_at BEFORE UPDATE ON action_policies FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS company_action_policies_set_updated_at ON company_action_policies;
CREATE TRIGGER company_action_policies_set_updated_at BEFORE UPDATE ON company_action_policies FOR EACH ROW EXECUTE FUNCTION set_updated_at();

INSERT INTO cron_jobs (job_key, schedule_type, interval_seconds, daily_time_utc, default_limit, next_run_at, metadata)
VALUES
  ('agent_runner', 'interval', 300, NULL, 3, now(), '{"description":"Pulse the local worker and reconcile queued work."}'::jsonb)
ON CONFLICT (job_key) DO NOTHING;

INSERT INTO addons (addon_key, title, status)
VALUES
  ('generated_apps', 'Generated Apps', 'available'),
  ('stripe', 'Stripe', 'available'),
  ('x_social', 'X Social', 'available'),
  ('meta_seedance', 'Meta Seedance', 'available'),
  ('community', 'Community Research', 'available'),
  ('outreach', 'Outreach', 'available')
ON CONFLICT (addon_key) DO NOTHING;

INSERT INTO action_policies (policy_key, title, default_requires_approval, metadata)
VALUES
  ('x.publish', 'Publish to X', false, '{"requires_real_receipt":true}'::jsonb),
  ('meta.launch', 'Launch Meta Campaign', true, '{"v0_forbidden":true}'::jsonb),
  ('community.post', 'Post to Community', true, '{"v0_forbidden":true}'::jsonb)
ON CONFLICT (policy_key) DO NOTHING;
