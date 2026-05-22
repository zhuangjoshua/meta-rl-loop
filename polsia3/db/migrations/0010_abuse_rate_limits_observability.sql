CREATE TABLE IF NOT EXISTS platform_rate_limit_buckets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  action text NOT NULL,
  bucket_key text NOT NULL,
  business_id uuid REFERENCES businesses(id) ON DELETE CASCADE,
  profile_id uuid REFERENCES profiles(id) ON DELETE SET NULL,
  app_user_id uuid REFERENCES generated_app_users(id) ON DELETE SET NULL,
  window_start timestamptz NOT NULL,
  window_seconds integer NOT NULL CHECK (window_seconds > 0),
  limit_count integer NOT NULL CHECK (limit_count >= 0),
  used_count integer NOT NULL DEFAULT 0 CHECK (used_count >= 0),
  blocked_count integer NOT NULL DEFAULT 0 CHECK (blocked_count >= 0),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (action, bucket_key, window_start)
);

CREATE TABLE IF NOT EXISTS platform_request_logs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid REFERENCES businesses(id) ON DELETE CASCADE,
  profile_id uuid REFERENCES profiles(id) ON DELETE SET NULL,
  app_user_id uuid REFERENCES generated_app_users(id) ON DELETE SET NULL,
  route text NOT NULL,
  method text NOT NULL,
  action text NOT NULL,
  status text NOT NULL DEFAULT 'completed',
  status_code integer NOT NULL DEFAULT 200,
  duration_ms integer NOT NULL DEFAULT 0,
  ip_hash text,
  user_agent text,
  request_id text,
  error text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE platform_request_logs DROP CONSTRAINT IF EXISTS platform_request_logs_status_check;
ALTER TABLE platform_request_logs
  ADD CONSTRAINT platform_request_logs_status_check
  CHECK (status IN ('completed', 'blocked', 'failed'));

CREATE INDEX IF NOT EXISTS platform_rate_limit_buckets_action_window_idx
  ON platform_rate_limit_buckets(action, window_start DESC);

CREATE INDEX IF NOT EXISTS platform_rate_limit_buckets_business_action_idx
  ON platform_rate_limit_buckets(business_id, action, window_start DESC);

CREATE INDEX IF NOT EXISTS platform_request_logs_business_created_idx
  ON platform_request_logs(business_id, created_at DESC);

CREATE INDEX IF NOT EXISTS platform_request_logs_action_created_idx
  ON platform_request_logs(action, created_at DESC);

CREATE INDEX IF NOT EXISTS platform_request_logs_status_created_idx
  ON platform_request_logs(status, created_at DESC);

DROP TRIGGER IF EXISTS platform_rate_limit_buckets_set_updated_at ON platform_rate_limit_buckets;
CREATE TRIGGER platform_rate_limit_buckets_set_updated_at
  BEFORE UPDATE ON platform_rate_limit_buckets
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
