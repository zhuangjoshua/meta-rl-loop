CREATE TABLE IF NOT EXISTS platform_integrations (
  id text PRIMARY KEY,
  status text NOT NULL DEFAULT 'not_configured',
  public_config jsonb NOT NULL DEFAULT '{}'::jsonb,
  encrypted_config jsonb NOT NULL DEFAULT '{}'::jsonb,
  key_version text NOT NULL DEFAULT 'v1',
  last_verified_at timestamptz,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS business_social_posts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  provider text NOT NULL,
  text text NOT NULL,
  provider_post_id text,
  provider_url text,
  status text NOT NULL DEFAULT 'proposed',
  action_id uuid,
  result jsonb NOT NULL DEFAULT '{}'::jsonb,
  error text,
  created_by_profile_id uuid REFERENCES profiles(id) ON DELETE SET NULL,
  published_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS company_payment_links (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  company_site_id uuid NOT NULL REFERENCES company_sites(id) ON DELETE CASCADE,
  plan_key text NOT NULL,
  name text NOT NULL,
  description text NOT NULL DEFAULT '',
  currency text NOT NULL DEFAULT 'usd',
  unit_amount_cents integer NOT NULL,
  billing_interval text NOT NULL DEFAULT 'month',
  stripe_product_id text NOT NULL,
  stripe_price_id text NOT NULL,
  stripe_payment_link_id text NOT NULL,
  stripe_payment_link_url text NOT NULL,
  active boolean NOT NULL DEFAULT true,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_by_profile_id uuid REFERENCES profiles(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (business_id, plan_key)
);

CREATE TABLE IF NOT EXISTS company_checkout_intents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  company_site_id uuid NOT NULL REFERENCES company_sites(id) ON DELETE CASCADE,
  company_payment_link_id uuid NOT NULL REFERENCES company_payment_links(id) ON DELETE CASCADE,
  stripe_payment_link_id text NOT NULL,
  client_reference_id text NOT NULL UNIQUE,
  status text NOT NULL DEFAULT 'started',
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS company_checkout_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  company_site_id uuid REFERENCES company_sites(id) ON DELETE SET NULL,
  company_payment_link_id uuid REFERENCES company_payment_links(id) ON DELETE SET NULL,
  checkout_intent_id uuid REFERENCES company_checkout_intents(id) ON DELETE SET NULL,
  stripe_checkout_session_id text NOT NULL UNIQUE,
  stripe_payment_link_id text,
  stripe_customer_id text,
  stripe_payment_intent_id text,
  stripe_subscription_id text,
  stripe_invoice_id text,
  mode text NOT NULL,
  payment_status text,
  status text,
  currency text,
  amount_subtotal_cents integer,
  amount_total_cents integer,
  client_reference_id text,
  customer_email text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  raw_event_id text,
  completed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS company_revenue_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  company_site_id uuid REFERENCES company_sites(id) ON DELETE SET NULL,
  company_payment_link_id uuid REFERENCES company_payment_links(id) ON DELETE SET NULL,
  checkout_intent_id uuid REFERENCES company_checkout_intents(id) ON DELETE SET NULL,
  provider text NOT NULL DEFAULT 'stripe',
  provider_event_id text,
  stripe_object_type text,
  stripe_object_id text,
  stripe_checkout_session_id text,
  stripe_invoice_id text,
  stripe_subscription_id text,
  stripe_customer_id text,
  revenue_type text NOT NULL,
  status text NOT NULL,
  currency text NOT NULL,
  amount_paid_cents integer NOT NULL DEFAULT 0,
  amount_due_cents integer,
  customer_email text,
  occurred_at timestamptz NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS webhook_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider text NOT NULL,
  provider_event_id text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  processed_at timestamptz,
  received_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (provider, provider_event_id)
);

CREATE TABLE IF NOT EXISTS media_generation_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  provider text NOT NULL,
  model text NOT NULL,
  status text NOT NULL DEFAULT 'queued',
  prompt text NOT NULL,
  input jsonb NOT NULL DEFAULT '{}'::jsonb,
  provider_job_id text,
  output_url text,
  storage_provider text NOT NULL DEFAULT 'atlas_url',
  stored_url text,
  content_type text NOT NULL DEFAULT 'video/mp4',
  result jsonb NOT NULL DEFAULT '{}'::jsonb,
  error text,
  submitted_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS growth_variants (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  experiment_id uuid,
  channel text NOT NULL,
  variant_type text NOT NULL,
  name text NOT NULL,
  prompt text,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'draft',
  score numeric,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS community_targets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  source text NOT NULL,
  title text NOT NULL,
  url text NOT NULL,
  match_reason text NOT NULL DEFAULT '',
  generated_copy text NOT NULL DEFAULT '',
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (business_id, url)
);

CREATE TABLE IF NOT EXISTS cold_outreach_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  lead_id uuid,
  event_type text NOT NULL,
  channel text NOT NULL,
  recipient text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS business_social_posts_business_created_idx ON business_social_posts(business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS media_generation_jobs_business_created_idx ON media_generation_jobs(business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS community_targets_business_created_idx ON community_targets(business_id, created_at DESC);
CREATE INDEX IF NOT EXISTS company_payment_links_business_idx ON company_payment_links(business_id, plan_key);
