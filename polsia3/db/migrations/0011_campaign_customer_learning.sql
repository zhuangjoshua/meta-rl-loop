ALTER TABLE business_social_posts
  ADD COLUMN IF NOT EXISTS campaign_id uuid REFERENCES business_campaigns(id) ON DELETE SET NULL;

ALTER TABLE media_generation_jobs
  ADD COLUMN IF NOT EXISTS campaign_id uuid REFERENCES business_campaigns(id) ON DELETE SET NULL;

ALTER TABLE business_email_messages
  ADD COLUMN IF NOT EXISTS campaign_id uuid REFERENCES business_campaigns(id) ON DELETE SET NULL;

ALTER TABLE cold_outreach_events
  ADD COLUMN IF NOT EXISTS campaign_id uuid REFERENCES business_campaigns(id) ON DELETE SET NULL;

ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS campaign_id uuid REFERENCES business_campaigns(id) ON DELETE SET NULL;

ALTER TABLE generated_app_product_runs
  ADD COLUMN IF NOT EXISTS campaign_id uuid REFERENCES business_campaigns(id) ON DELETE SET NULL;

ALTER TABLE company_checkout_intents
  ADD COLUMN IF NOT EXISTS campaign_id uuid REFERENCES business_campaigns(id) ON DELETE SET NULL;

ALTER TABLE company_checkout_sessions
  ADD COLUMN IF NOT EXISTS campaign_id uuid REFERENCES business_campaigns(id) ON DELETE SET NULL;

ALTER TABLE company_revenue_events
  ADD COLUMN IF NOT EXISTS campaign_id uuid REFERENCES business_campaigns(id) ON DELETE SET NULL;

ALTER TABLE growth_variants
  ADD COLUMN IF NOT EXISTS campaign_id uuid REFERENCES business_campaigns(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS campaign_metric_snapshots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  campaign_id uuid REFERENCES business_campaigns(id) ON DELETE SET NULL,
  channel text NOT NULL,
  source_type text NOT NULL,
  source_id text NOT NULL DEFAULT '',
  provider text,
  provider_object_id text,
  observed_at timestamptz NOT NULL DEFAULT now(),
  spend_microusd bigint NOT NULL DEFAULT 0,
  impressions bigint NOT NULL DEFAULT 0,
  clicks bigint NOT NULL DEFAULT 0,
  replies bigint NOT NULL DEFAULT 0,
  conversions bigint NOT NULL DEFAULT 0,
  customers bigint NOT NULL DEFAULT 0,
  revenue_cents bigint NOT NULL DEFAULT 0,
  raw jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS customer_response_signals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  campaign_id uuid REFERENCES business_campaigns(id) ON DELETE SET NULL,
  source_type text NOT NULL,
  source_id text NOT NULL DEFAULT '',
  customer_key text,
  channel text NOT NULL,
  response_type text NOT NULL,
  sentiment text NOT NULL DEFAULT 'unknown',
  intent text NOT NULL DEFAULT 'unknown',
  signal_strength numeric NOT NULL DEFAULT 0,
  content_excerpt text NOT NULL DEFAULT '',
  raw jsonb NOT NULL DEFAULT '{}'::jsonb,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE customer_response_signals DROP CONSTRAINT IF EXISTS customer_response_signals_sentiment_check;
ALTER TABLE customer_response_signals
  ADD CONSTRAINT customer_response_signals_sentiment_check
  CHECK (sentiment IN ('positive', 'neutral', 'negative', 'unknown'));

ALTER TABLE customer_response_signals DROP CONSTRAINT IF EXISTS customer_response_signals_intent_check;
ALTER TABLE customer_response_signals
  ADD CONSTRAINT customer_response_signals_intent_check
  CHECK (intent IN ('buying', 'activation', 'support', 'objection', 'unsubscribe', 'interest', 'unknown'));

CREATE UNIQUE INDEX IF NOT EXISTS customer_response_signals_source_uidx
  ON customer_response_signals(business_id, source_type, source_id, response_type);

CREATE INDEX IF NOT EXISTS campaign_metric_snapshots_business_observed_idx
  ON campaign_metric_snapshots(business_id, observed_at DESC);

CREATE INDEX IF NOT EXISTS campaign_metric_snapshots_campaign_channel_idx
  ON campaign_metric_snapshots(campaign_id, channel, observed_at DESC);

CREATE INDEX IF NOT EXISTS customer_response_signals_business_occurred_idx
  ON customer_response_signals(business_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS customer_response_signals_campaign_channel_idx
  ON customer_response_signals(campaign_id, channel, occurred_at DESC);

CREATE INDEX IF NOT EXISTS business_social_posts_campaign_created_idx
  ON business_social_posts(campaign_id, created_at DESC);

CREATE INDEX IF NOT EXISTS media_generation_jobs_campaign_created_idx
  ON media_generation_jobs(campaign_id, created_at DESC);

CREATE INDEX IF NOT EXISTS business_email_messages_campaign_created_idx
  ON business_email_messages(campaign_id, created_at DESC);

CREATE INDEX IF NOT EXISTS cold_outreach_events_campaign_created_idx
  ON cold_outreach_events(campaign_id, created_at DESC);

CREATE INDEX IF NOT EXISTS leads_campaign_created_idx
  ON leads(campaign_id, created_at DESC);

CREATE INDEX IF NOT EXISTS generated_app_product_runs_campaign_created_idx
  ON generated_app_product_runs(campaign_id, created_at DESC);

CREATE INDEX IF NOT EXISTS company_revenue_events_campaign_occurred_idx
  ON company_revenue_events(campaign_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS growth_variants_campaign_created_idx
  ON growth_variants(campaign_id, created_at DESC);
